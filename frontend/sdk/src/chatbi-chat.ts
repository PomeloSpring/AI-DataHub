import { ChatBIClient } from './api-client';
import { applyTheme, ChatBITheme } from './theme';
import baseStyles from './styles/base.css?inline';

export class ChatBIChat extends HTMLElement {
  private shadow: ShadowRoot;
  private client: ChatBIClient | null = null;
  private messages: Array<{ role: string; content: string; sql?: string; result?: any }> = [];
  private conversationId: number | null = null;
  private loading = false;
  private loadingStep = '';

  static get observedAttributes() {
    return ['host', 'api-key', 'user-id', 'user-name', 'theme', 'height', 'locale'];
  }

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback() {
    this.init();
  }

  disconnectedCallback() {
    this.client?.dispose();
  }

  attributeChangedCallback() {
    if (this.isConnected && this.client) {
      this.client.dispose();
      this.init();
    }
  }

  private getAttr(name: string): string {
    return this.getAttribute(name) || '';
  }

  private async init() {
    const host = this.getAttr('host');
    const apiKey = this.getAttr('api-key');
    const userId = this.getAttr('user-id');
    const userName = this.getAttr('user-name');
    const height = this.getAttr('height') || '600px';

    if (!host || !apiKey || !userId) {
      this.renderError('缺少必要属性：host, api-key, user-id');
      return;
    }

    // Apply theme
    let theme: ChatBITheme | undefined;
    try {
      const themeStr = this.getAttr('theme');
      if (themeStr) theme = JSON.parse(themeStr);
    } catch {}

    applyTheme(this.shadow, theme);

    // Inject styles
    const styleEl = document.createElement('style');
    styleEl.textContent = baseStyles;
    this.shadow.appendChild(styleEl);

    // Set height
    this.style.display = 'block';
    this.style.height = height;

    // Init client
    this.client = new ChatBIClient(host, apiKey, userId, userName);
    try {
      await this.client.init();
    } catch (e: any) {
      this.renderError(e.message || '认证失败');
      return;
    }

    // Auto-create a single conversation
    try {
      const conv = await this.client.createConversation();
      this.conversationId = conv.id;
    } catch {
      // silent, will work without conversation tracking
    }

    // Render chat UI
    this.render();
    this.emit('chatbi:ready', { appId: 0, userId });
  }

  private renderError(message: string) {
    this.shadow.innerHTML = `
      <style>${baseStyles}</style>
      <div class="chatbi-container">
        <div class="chatbi-body" style="display:flex;align-items:center;justify-content:center;">
          <div class="chatbi-error">${message}</div>
        </div>
      </div>
    `;
  }

  private render() {
    const existing = this.shadow.querySelector('.chatbi-container');
    const container = existing || document.createElement('div');
    container.className = 'chatbi-container';
    container.innerHTML = `
      <div class="chatbi-header">
        <span class="chatbi-header-title">💬 ChatBI 数据分析</span>
        <button class="chatbi-btn" id="chatbi-clear-btn" style="padding:4px 12px;font-size:12px;background:transparent;color:var(--chatbi-text-secondary);border:1px solid var(--chatbi-border);">清空</button>
      </div>
      <div class="chatbi-body" id="chatbi-messages">
        ${this.messages.length === 0 ? `
          <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:32px;margin-bottom:16px;">📊</div>
            <div style="font-size:16px;font-weight:500;margin-bottom:8px;">ChatBI 数据分析助手</div>
            <div style="color:var(--chatbi-text-secondary);font-size:13px;">输入你的数据问题，AI帮你生成SQL并分析</div>
          </div>
        ` : ''}
        ${this.messages.map(m => this.renderMessage(m)).join('')}
        ${this.loading ? `<div class="chatbi-loading"><div class="chatbi-loading-dot"></div><div class="chatbi-loading-dot"></div><div class="chatbi-loading-dot"></div><span>${this.loadingStep}</span></div>` : ''}
      </div>
      <div class="chatbi-input-area">
        <input class="chatbi-input" id="chatbi-input" placeholder="输入你的数据问题..." ${this.loading ? 'disabled' : ''} />
        <button class="chatbi-btn" id="chatbi-send-btn" ${this.loading ? 'disabled' : ''}>发送</button>
      </div>
    `;

    if (!existing) {
      this.shadow.appendChild(container);
    }

    // Bind events
    const input = this.shadow.getElementById('chatbi-input') as HTMLInputElement;
    const sendBtn = this.shadow.getElementById('chatbi-send-btn');
    const clearBtn = this.shadow.getElementById('chatbi-clear-btn');

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !this.loading) this.handleSend();
    });
    sendBtn?.addEventListener('click', () => this.handleSend());
    clearBtn?.addEventListener('click', () => this.handleClear());

    // Scroll to bottom
    this.scrollToBottom();
  }

  private renderMessage(msg: { role: string; content: string; sql?: string; result?: any }): string {
    const isUser = msg.role === 'user';
    let html = `<div class="chatbi-msg ${isUser ? 'chatbi-msg-user' : 'chatbi-msg-assistant'}">`;

    if (isUser) {
      html += `<div>${this.escapeHtml(msg.content)}</div>`;
    } else {
      // Show interpretation if available, otherwise show content
      const interpretation = msg.result?.interpretation;
      const displayText = interpretation || msg.content;
      html += `<div>${this.escapeHtml(displayText)}</div>`;

      if (msg.sql) {
        html += `<div class="chatbi-sql-block">${this.escapeHtml(msg.sql)}</div>`;
      }

      if (msg.result?.columns && msg.result?.rows) {
        html += this.renderTable(msg.result.columns, msg.result.rows);
      }
    }

    html += '</div>';
    return html;
  }

  private renderTable(columns: string[], rows: any[]): string {
    let html = '<table class="chatbi-result-table"><thead><tr>';
    columns.forEach(c => { html += `<th>${this.escapeHtml(c)}</th>`; });
    html += '</tr></thead><tbody>';
    rows.slice(0, 100).forEach(row => {
      html += '<tr>';
      columns.forEach(c => { html += `<td>${this.escapeHtml(String(row[c] ?? ''))}</td>`; });
      html += '</tr>';
    });
    if (rows.length > 100) {
      html += `<tr><td colspan="${columns.length}" style="text-align:center;color:var(--chatbi-text-secondary);">... 共 ${rows.length} 条</td></tr>`;
    }
    html += '</tbody></table>';
    return html;
  }

  private async handleSend() {
    const input = this.shadow.getElementById('chatbi-input') as HTMLInputElement;
    const question = input?.value?.trim();
    if (!question || this.loading || !this.client) return;

    input.value = '';
    this.messages.push({ role: 'user', content: question });
    this.loading = true;
    this.loadingStep = '思考中...';
    this.render();

    try {
      const response = await this.client.sendMessage(question, this.conversationId || undefined);
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) throw new Error('无法读取响应');

      let assistantContent = '';
      let assistantSql = '';
      let assistantResult: any = null;
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value, { stream: true });
        const lines = text.split('\n');

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            continue;
          }
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));

              // Progress events — update loading step
              if (currentEvent === 'progress' && data.message) {
                this.loadingStep = data.message;
                this.render();
              }

              // Token events — streaming text
              if (currentEvent === 'token' && data.text) {
                assistantContent += data.text;
              }

              // Thinking events
              if (currentEvent === 'thinking' && data.text) {
                // Optional: could show thinking text
              }

              // Done events — final result
              if (currentEvent === 'done') {
                if (data.reply !== undefined) assistantContent = data.reply;
                if (data.sql) assistantSql = data.sql;
                if (data.result) assistantResult = data.result;
              }

              // Error events
              if (currentEvent === 'error') {
                this.emit('chatbi:error', { code: 0, message: data.message });
              }
            } catch {}
          }
        }
      }

      this.messages.push({
        role: 'assistant',
        content: assistantContent,
        sql: assistantSql || undefined,
        result: assistantResult || undefined,
      });

      this.emit('chatbi:message', {
        conversationId: this.conversationId,
        content: assistantContent,
        sql: assistantSql,
        result: assistantResult,
      });

    } catch (e: any) {
      this.messages.push({
        role: 'assistant',
        content: `错误：${e.message || '请求失败'}`,
      });
      this.emit('chatbi:error', { code: 0, message: e.message });
    }

    this.loading = false;
    this.loadingStep = '';
    this.render();
  }

  private async handleClear() {
    this.messages = [];
    // Create a new conversation
    if (this.client) {
      try {
        const conv = await this.client.createConversation();
        this.conversationId = conv.id;
      } catch {}
    }
    this.render();
  }

  private scrollToBottom() {
    const msgContainer = this.shadow.getElementById('chatbi-messages');
    if (msgContainer) {
      requestAnimationFrame(() => {
        msgContainer.scrollTop = msgContainer.scrollHeight;
      });
    }
  }

  private emit(name: string, detail: any) {
    this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
  }

  private escapeHtml(str: string): string {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Public JS API ───────────────────────────────────────────────

  sendMessagePublic(question: string) {
    const input = this.shadow.getElementById('chatbi-input') as HTMLInputElement;
    if (input) {
      input.value = question;
      this.handleSend();
    }
  }
}

// Register custom element
if (!customElements.get('chatbi-chat')) {
  customElements.define('chatbi-chat', ChatBIChat);
}
