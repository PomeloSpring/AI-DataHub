import { ChatBIClient } from './api-client';
import { applyTheme, ChatBITheme } from './theme';
import baseStyles from './styles/base.css?inline';

export class ChatBIDashboard extends HTMLElement {
  private shadow: ShadowRoot;
  private client: ChatBIClient | null = null;
  private dashboard: any = null;
  private dashboards: any[] = [];

  static get observedAttributes() {
    return ['host', 'api-key', 'user-id', 'user-name', 'theme', 'height', 'dashboard-id'];
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
    const height = this.getAttr('height') || '800px';
    const dashboardId = this.getAttr('dashboard-id');

    if (!host || !apiKey || !userId) {
      this.renderError('缺少必要属性：host, api-key, user-id');
      return;
    }

    let theme: ChatBITheme | undefined;
    try {
      const themeStr = this.getAttr('theme');
      if (themeStr) theme = JSON.parse(themeStr);
    } catch {}

    applyTheme(this.shadow, theme);

    const styleEl = document.createElement('style');
    styleEl.textContent = baseStyles;
    this.shadow.appendChild(styleEl);

    this.style.display = 'block';
    this.style.height = height;

    this.client = new ChatBIClient(host, apiKey, userId, userName);
    try {
      await this.client.init();
    } catch (e: any) {
      this.renderError(e.message || '认证失败');
      return;
    }

    if (dashboardId) {
      await this.loadDashboard(parseInt(dashboardId));
    } else {
      await this.loadDashboardList();
    }
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

  private async loadDashboardList() {
    if (!this.client) return;

    try {
      const data = await this.client.getDashboards();
      this.dashboards = data.items || [];
      this.renderDashboardList();
    } catch (e: any) {
      this.renderError(e.message || '加载仪表盘列表失败');
    }
  }

  private renderDashboardList() {
    const existing = this.shadow.querySelector('.chatbi-container');
    const container = existing || document.createElement('div');
    container.className = 'chatbi-container';
    container.innerHTML = `
      <div class="chatbi-header">
        <span class="chatbi-header-title">📊 仪表盘</span>
      </div>
      <div class="chatbi-body">
        ${this.dashboards.length === 0
          ? '<div style="text-align:center;color:var(--chatbi-text-secondary);padding:40px;">暂无可用仪表盘</div>'
          : `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;">
              ${this.dashboards.map(d => `
                <div class="chatbi-dashboard-card" data-id="${d.id}" style="
                  padding:20px;
                  border:1px solid var(--chatbi-border);
                  border-radius:var(--chatbi-radius);
                  cursor:pointer;
                  transition:all 0.2s;
                ">
                  <div style="font-weight:600;margin-bottom:8px;">${this.escapeHtml(d.name)}</div>
                  <div style="font-size:13px;color:var(--chatbi-text-secondary);">${this.escapeHtml(d.description || '暂无描述')}</div>
                </div>
              `).join('')}
            </div>`
        }
      </div>
    `;

    if (!existing) {
      this.shadow.appendChild(container);
    }

    // Bind click events
    container.querySelectorAll('.chatbi-dashboard-card').forEach(card => {
      card.addEventListener('click', () => {
        const id = parseInt(card.getAttribute('data-id') || '0');
        if (id) this.loadDashboard(id);
      });
      card.addEventListener('mouseenter', () => {
        (card as HTMLElement).style.borderColor = 'var(--chatbi-primary)';
        (card as HTMLElement).style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
      });
      card.addEventListener('mouseleave', () => {
        (card as HTMLElement).style.borderColor = 'var(--chatbi-border)';
        (card as HTMLElement).style.boxShadow = 'none';
      });
    });
  }

  private async loadDashboard(id: number) {
    if (!this.client) return;

    try {
      this.dashboard = await this.client.getDashboard(id);
      this.renderDashboard();
    } catch (e: any) {
      this.renderError(e.message || '加载仪表盘失败');
    }
  }

  private renderDashboard() {
    if (!this.dashboard) return;

    const existing = this.shadow.querySelector('.chatbi-container');
    const container = existing || document.createElement('div');
    container.className = 'chatbi-container';
    container.innerHTML = `
      <div class="chatbi-header">
        <span>
          <button class="chatbi-btn" id="chatbi-back-btn" style="padding:4px 12px;font-size:12px;margin-right:12px;">← 返回</button>
          <span class="chatbi-header-title">${this.escapeHtml(this.dashboard.name)}</span>
        </span>
        ${this.dashboard.description ? `<span style="font-size:13px;color:var(--chatbi-text-secondary);">${this.escapeHtml(this.dashboard.description)}</span>` : ''}
      </div>
      <div class="chatbi-body">
        ${(this.dashboard.charts || []).map((chart: any) => `
          <div style="
            margin-bottom:24px;
            padding:20px;
            border:1px solid var(--chatbi-border);
            border-radius:var(--chatbi-radius);
            background:var(--chatbi-bg);
          ">
            <div style="font-weight:600;margin-bottom:12px;">${this.escapeHtml(chart.name)}</div>
            <div style="font-size:13px;color:var(--chatbi-text-secondary);">
              图表类型: <span class="chatbi-badge chatbi-badge-info">${chart.chart_type}</span>
            </div>
            ${chart.sql_query ? `<div class="chatbi-sql-block" style="margin-top:12px;">${this.escapeHtml(chart.sql_query)}</div>` : ''}
          </div>
        `).join('')}
        ${(!this.dashboard.charts || this.dashboard.charts.length === 0)
          ? '<div style="text-align:center;color:var(--chatbi-text-secondary);padding:40px;">该仪表盘暂无图表</div>'
          : ''
        }
      </div>
    `;

    if (!existing) {
      this.shadow.appendChild(container);
    }

    const backBtn = this.shadow.getElementById('chatbi-back-btn');
    backBtn?.addEventListener('click', () => this.loadDashboardList());
  }

  private escapeHtml(str: string): string {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
}

if (!customElements.get('chatbi-dashboard')) {
  customElements.define('chatbi-dashboard', ChatBIDashboard);
}
